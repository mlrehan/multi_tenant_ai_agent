"use client";

import { use as usePromise, useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
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
import {
  useCreateCustomRole,
  useCreateOverride,
  useCreateRoleHierarchyEdge,
  useEditRolePermissions,
  useTenantEffectivePermissions,
  useTenantPermissionCatalog,
  useTenantRolePermissions,
  useTenantRoles,
} from "@/features/rbac/hooks";
import { useTenantMembers } from "@/features/tenancy/hooks";
import { isApiError } from "@/lib/api-client";
import type { OverrideEffect, RoleSummary } from "@/lib/types";

export default function RbacPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);

  return (
    <div>
      <PageHeader
        title="Roles &amp; permissions"
        description="Define custom roles, build role hierarchies, and grant or deny individual permissions."
      />

      <Tabs defaultValue="roles">
        <TabsList>
          <TabsTrigger value="roles">Roles</TabsTrigger>
          <TabsTrigger value="hierarchy">Hierarchy</TabsTrigger>
          <TabsTrigger value="overrides">Overrides</TabsTrigger>
          <TabsTrigger value="mine">My permissions</TabsTrigger>
          <TabsTrigger value="catalog">Catalog</TabsTrigger>
        </TabsList>

        <TabsContent value="roles" className="mt-4">
          <RolesTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="hierarchy" className="mt-4">
          <HierarchyTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="overrides" className="mt-4">
          <OverridesTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="mine" className="mt-4">
          <MyPermissionsTab tenantId={tenantId} />
        </TabsContent>
        <TabsContent value="catalog" className="mt-4">
          <CatalogTab tenantId={tenantId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---- Roles ----

const roleSchema = z.object({
  code: z
    .string()
    .min(2, "Use at least 2 characters.")
    .regex(/^[a-z0-9_]+$/, "Lowercase letters, numbers, and underscores only."),
  name: z.string().min(1, "Enter a name."),
  description: z.string().optional(),
  rank: z.coerce.number().int().min(0, "Rank must be 0 or higher."),
});
type RoleForm = z.infer<typeof roleSchema>;

function RolesTab({ tenantId }: { tenantId: string }) {
  const { data: roles, isLoading, error } = useTenantRoles(tenantId);
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger render={<Button size="sm" />}>
            <Plus />
            New custom role
          </DialogTrigger>
          <CreateRoleDialog tenantId={tenantId} onDone={() => setOpen(false)} />
        </Dialog>
      </div>

      {isLoading && <TableSkeleton rows={4} columns={4} />}
      {error && <ErrorState error={error} resource="roles" />}
      {roles && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Code</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead className="text-right">Rank</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {roles.map((role) => (
                  <RoleRow key={role.id} tenantId={tenantId} role={role} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function RoleRow({ tenantId, role }: { tenantId: string; role: RoleSummary }) {
  const [editOpen, setEditOpen] = useState(false);

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{role.code}</TableCell>
      <TableCell className="font-medium">{role.name}</TableCell>
      <TableCell className="text-right tabular-nums">{role.rank}</TableCell>
      <TableCell>
        <Badge variant={role.is_system ? "outline" : "secondary"}>
          {role.is_system ? "System" : "Custom"}
        </Badge>
      </TableCell>
      <TableCell>
        <Dialog open={editOpen} onOpenChange={setEditOpen}>
          <DialogTrigger render={<Button size="xs" variant="ghost" disabled={role.is_system} />}>
            {role.is_system ? "Fixed" : "Edit"}
          </DialogTrigger>
          <EditTenantRolePermissionsDialog tenantId={tenantId} role={role} />
        </Dialog>
      </TableCell>
    </TableRow>
  );
}

function EditTenantRolePermissionsDialog({
  tenantId,
  role,
}: {
  tenantId: string;
  role: RoleSummary;
}) {
  const catalog = useTenantPermissionCatalog(tenantId);
  const rolePermissions = useTenantRolePermissions(tenantId);
  const { add, remove } = useEditRolePermissions(tenantId);

  const assigned = new Set(rolePermissions.data?.by_role_code[role.code] ?? []);
  // Only tenant-customizable permissions can be added to a custom role -- the
  // backend rejects the rest with 409, same rule as role creation.
  const assignable = (catalog.data ?? []).filter((p) => p.tenant_customizable);

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
            permissions={assignable}
            selected={assigned}
            onToggle={(code) => toggle(code, assigned.has(code))}
            emptyMessage="No tenant-customizable permissions are available."
          />
        )}
      </div>
    </DialogContent>
  );
}

function CreateRoleDialog({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const createRole = useCreateCustomRole(tenantId);
  const { data: catalog } = useTenantPermissionCatalog(tenantId);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<RoleForm>({ resolver: zodResolver(roleSchema), defaultValues: { rank: 10 } });

  // Only tenant-customizable permissions may appear on a custom role -- the
  // backend rejects the rest with 409, so filtering here turns a confusing
  // server error into an option that was never offered.
  const assignable = (catalog ?? []).filter((p) => p.tenant_customizable);

  function toggle(code: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function onSubmit(values: RoleForm) {
    try {
      await createRole.mutateAsync({
        code: values.code,
        name: values.name,
        description: values.description?.trim() || null,
        rank: values.rank,
        permissionCodes: [...selected],
      });
      toast.success(`Created role ${values.code}`);
      reset();
      setSelected(new Set());
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the role.");
    }
  }

  return (
    <DialogContent className="max-w-2xl">
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogHeader>
          <DialogTitle>New custom role</DialogTitle>
          <DialogDescription>
            You can only include permissions you hold yourself.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="role-code">Code</Label>
              <Input id="role-code" placeholder="content_editor" className="mt-1.5 font-mono text-xs" {...register("code")} />
              <FieldError message={errors.code?.message} />
            </div>
            <div>
              <Label htmlFor="role-name">Name</Label>
              <Input id="role-name" placeholder="Content Editor" className="mt-1.5" {...register("name")} />
              <FieldError message={errors.name?.message} />
            </div>
            <div>
              <Label htmlFor="role-rank">Rank</Label>
              <Input id="role-rank" type="number" className="mt-1.5 tabular-nums" {...register("rank")} />
              <FieldError message={errors.rank?.message} />
            </div>
          </div>
          <div>
            <Label htmlFor="role-description">Description</Label>
            <Input id="role-description" className="mt-1.5" {...register("description")} />
          </div>
          <div>
            <Label>Permissions ({selected.size} selected)</Label>
            <div className="mt-1.5 max-h-64 overflow-y-auto rounded-md border border-border p-3">
              <PermissionList
                permissions={assignable}
                selected={selected}
                onToggle={toggle}
                emptyMessage="No tenant-customizable permissions are available."
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={createRole.isPending}>
            {createRole.isPending ? "Creating…" : "Create role"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

// ---- Hierarchy ----

function HierarchyTab({ tenantId }: { tenantId: string }) {
  const { data: roles } = useTenantRoles(tenantId);
  const createEdge = useCreateRoleHierarchyEdge(tenantId);
  const [parent, setParent] = useState("");
  const [child, setChild] = useState("");

  async function handleCreate() {
    try {
      await createEdge.mutateAsync({ parent, child });
      toast.success(`${parent} now inherits ${child}`);
      setParent("");
      setChild("");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the relationship.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Role inheritance</CardTitle>
        <CardDescription>
          A parent role inherits every permission its child holds. Cycles are rejected, and you
          can&apos;t create an edge that would give a role more than you hold yourself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="parent-role">Parent role (inherits)</Label>
            <Select value={parent} onValueChange={(v) => setParent(v ?? "")}>
              <SelectTrigger id="parent-role" className="mt-1.5 w-full">
                <SelectValue placeholder="Select a role" />
              </SelectTrigger>
              <SelectContent>
                {roles?.map((r) => (
                  <SelectItem key={r.id} value={r.code}>
                    {r.name} ({r.code})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="child-role">Child role (inherited from)</Label>
            <Select value={child} onValueChange={(v) => setChild(v ?? "")}>
              <SelectTrigger id="child-role" className="mt-1.5 w-full">
                <SelectValue placeholder="Select a role" />
              </SelectTrigger>
              <SelectContent>
                {roles
                  ?.filter((r) => r.code !== parent)
                  .map((r) => (
                    <SelectItem key={r.id} value={r.code}>
                      {r.name} ({r.code})
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <Button
          size="sm"
          disabled={!parent || !child || createEdge.isPending}
          onClick={handleCreate}
        >
          {createEdge.isPending ? "Creating…" : "Create inheritance"}
        </Button>
      </CardContent>
    </Card>
  );
}

// ---- Overrides ----

function OverridesTab({ tenantId }: { tenantId: string }) {
  const { data: members } = useTenantMembers(tenantId);
  const { data: catalog } = useTenantPermissionCatalog(tenantId);
  const createOverride = useCreateOverride(tenantId);
  const [membershipId, setMembershipId] = useState("");
  const [permissionCode, setPermissionCode] = useState("");
  const [effect, setEffect] = useState<OverrideEffect>("deny");
  const [reason, setReason] = useState("");

  const isValid = membershipId && permissionCode && reason.trim().length > 0;

  async function handleCreate() {
    try {
      await createOverride.mutateAsync({
        targetMembershipId: membershipId,
        permissionCode,
        effect,
        reason: reason.trim(),
        expiresAt: null,
      });
      toast.success(`Override created: ${effect} ${permissionCode}`);
      setReason("");
      setPermissionCode("");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the override.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Permission overrides</CardTitle>
        <CardDescription>
          Grant or deny a single permission for one member, independent of their roles. A deny
          always wins over any grant.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="override-member">Member</Label>
            <Select value={membershipId} onValueChange={(v) => setMembershipId(v ?? "")}>
              <SelectTrigger id="override-member" className="mt-1.5 w-full">
                <SelectValue placeholder="Select a member" />
              </SelectTrigger>
              <SelectContent>
                {members?.map((m) => (
                  <SelectItem key={m.membership_id} value={m.membership_id}>
                    {m.user_id.slice(0, 8)}… ({m.status})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="override-permission">Permission</Label>
            <Select value={permissionCode} onValueChange={(v) => setPermissionCode(v ?? "")}>
              <SelectTrigger id="override-permission" className="mt-1.5 w-full">
                <SelectValue placeholder="Select a permission" />
              </SelectTrigger>
              <SelectContent>
                {catalog?.map((p) => (
                  <SelectItem key={p.code} value={p.code}>
                    {p.code}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="override-effect">Effect</Label>
            <Select value={effect} onValueChange={(v) => setEffect(v as OverrideEffect)}>
              <SelectTrigger id="override-effect" className="mt-1.5 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="deny">Deny</SelectItem>
                <SelectItem value="allow">Allow</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="override-reason">Reason</Label>
            <Input
              id="override-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Temporary restriction during review"
              className="mt-1.5"
            />
          </div>
        </div>
        <Button size="sm" disabled={!isValid || createOverride.isPending} onClick={handleCreate}>
          {createOverride.isPending ? "Creating…" : "Create override"}
        </Button>
        <p className="text-xs text-muted-foreground">
          Listing existing overrides isn&apos;t available from the API yet — this creates new ones
          only.
        </p>
      </CardContent>
    </Card>
  );
}

// ---- Read-only views ----

function MyPermissionsTab({ tenantId }: { tenantId: string }) {
  const mine = useTenantEffectivePermissions(tenantId);
  const catalog = useTenantPermissionCatalog(tenantId);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your effective permissions in this tenant</CardTitle>
        <CardDescription>
          The fully resolved set — roles, inherited roles, and overrides combined.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {mine.isLoading && <TableSkeleton rows={4} columns={1} />}
        {mine.error && <ErrorState error={mine.error} resource="your permissions" />}
        {mine.data && catalog.data && (
          <PermissionList
            permissions={catalog.data.filter((p) => mine.data.permissions.includes(p.code))}
            emptyMessage="You hold no permissions in this tenant."
          />
        )}
      </CardContent>
    </Card>
  );
}

function CatalogTab({ tenantId }: { tenantId: string }) {
  const catalog = useTenantPermissionCatalog(tenantId);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tenant permission catalog</CardTitle>
        <CardDescription>Every tenant-scope permission defined on this deployment.</CardDescription>
      </CardHeader>
      <CardContent>
        {catalog.isLoading && <TableSkeleton rows={5} columns={1} />}
        {catalog.error && <ErrorState error={catalog.error} resource="the permission catalog" />}
        {catalog.data && <PermissionList permissions={catalog.data} />}
      </CardContent>
    </Card>
  );
}
