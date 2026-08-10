"use client";

import { useState } from "react";
import { Building2, Check, Pencil, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
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
  useCreateTenant,
  useReactivateTenant,
  useRenameTenant,
  useSuspendTenant,
  useTenants,
} from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import type { Tenant } from "@/lib/types";

export default function PlatformTenantsPage() {
  const { data: tenants, isLoading, error } = useTenants();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div>
      <PageHeader
        eyebrow="Platform"
        title="Tenants"
        description="Every tenant on the platform. Creating a tenant also creates its first membership and grants the owner role."
        actions={
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              New tenant
            </DialogTrigger>
            <CreateTenantDialog onDone={() => setCreateOpen(false)} />
          </Dialog>
        }
      />

      {isLoading && <TableSkeleton rows={4} columns={5} />}
      {error && <ErrorState error={error} resource="tenants" scope="platform" />}

      {tenants && tenants.length === 0 && (
        <EmptyState
          icon={Building2}
          title="No tenants yet"
          description="Create the first tenant to start onboarding an organization."
          action={
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus />
              New tenant
            </Button>
          }
        />
      )}

      {tenants && tenants.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Slug</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Tenant ID</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenants.map((tenant) => (
                  <TenantRow key={tenant.id} tenant={tenant} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function TenantRow({ tenant }: { tenant: Tenant }) {
  const suspend = useSuspendTenant();
  const reactivate = useReactivateTenant();
  const rename = useRenameTenant();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [renameOpen, setRenameOpen] = useState(false);
  const [displayName, setDisplayName] = useState(tenant.display_name);

  const isSuspended = tenant.status === "suspended";
  const canSuspend = tenant.status === "active";

  async function handleRename() {
    try {
      await rename.mutateAsync({ tenantId: tenant.id, displayName: displayName.trim() });
      toast.success("Tenant renamed");
      setRenameOpen(false);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't rename the tenant.");
    }
  }

  async function handleSuspend() {
    try {
      await suspend.mutateAsync({ tenantId: tenant.id, reason });
      toast.success(`Suspended ${tenant.display_name}`);
      setOpen(false);
      setReason("");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't suspend the tenant.");
    }
  }

  async function handleReactivate() {
    try {
      await reactivate.mutateAsync(tenant.id);
      toast.success(`Reactivated ${tenant.display_name}`);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't reactivate the tenant.");
    }
  }

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{tenant.slug}</TableCell>
      <TableCell className="font-medium">{tenant.display_name}</TableCell>
      <TableCell>
        <StatusBadge status={tenant.status} />
        {tenant.suspended_reason && (
          <p className="mt-1 text-xs text-muted-foreground">{tenant.suspended_reason}</p>
        )}
      </TableCell>
      <TableCell>
        <IdentityChip value={tenant.id} label="tenant" />
      </TableCell>
      <TableCell className="text-right">
        <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
          <DialogTrigger render={<Button size="xs" variant="ghost" className="mr-1.5" />}>
            <Pencil className="size-3" />
            Rename
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Rename {tenant.display_name}</DialogTitle>
              <DialogDescription>
                Only the display name changes — the URL slug (
                <code className="font-mono">{tenant.slug}</code>) is fixed once a tenant is
                created, since it&apos;s baked into links and API references.
              </DialogDescription>
            </DialogHeader>
            <div>
              <Label htmlFor={`rename-${tenant.id}`}>Organization name</Label>
              <Input
                id={`rename-${tenant.id}`}
                autoFocus
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="mt-1.5"
              />
            </div>
            <DialogFooter>
              <Button variant="outline" size="sm" onClick={() => setRenameOpen(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleRename}
                disabled={
                  !displayName.trim() ||
                  displayName.trim() === tenant.display_name ||
                  rename.isPending
                }
              >
                {rename.isPending ? "Saving…" : "Save"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {isSuspended ? (
          <Button
            size="xs"
            variant="outline"
            onClick={handleReactivate}
            disabled={reactivate.isPending}
          >
            {reactivate.isPending ? "Reactivating…" : "Reactivate"}
          </Button>
        ) : (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" disabled={!canSuspend} />}>
              Suspend
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Suspend {tenant.display_name}?</DialogTitle>
                <DialogDescription>
                  Members lose access immediately. You can reactivate the tenant later.
                </DialogDescription>
              </DialogHeader>
              <div>
                <Label htmlFor={`reason-${tenant.id}`}>Reason</Label>
                <Input
                  id={`reason-${tenant.id}`}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Non-payment, policy violation, …"
                  className="mt-1.5"
                />
              </div>
              <DialogFooter>
                <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={handleSuspend}
                  disabled={!reason.trim() || suspend.isPending}
                >
                  {suspend.isPending ? "Suspending…" : "Suspend tenant"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </TableCell>
    </TableRow>
  );
}

/** Mirrors the backend's `^[a-z0-9-]+$` rule: lowercase, collapse runs of
 * anything else into single hyphens, trim the ends. */
export function slugify(displayName: string): string {
  return displayName
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

function CreateTenantDialog({ onDone }: { onDone: () => void }) {
  const createTenant = useCreateTenant();
  const { data: existingTenants } = useTenants();

  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  // Once the operator edits the slug themselves, stop overwriting it from the
  // display name -- silently clobbering a deliberate choice is worse than
  // making them fix it once.
  const [slugTouched, setSlugTouched] = useState(false);
  const [ownerUserId, setOwnerUserId] = useState("");

  const effectiveSlug = slugTouched ? slug : slugify(displayName);
  const slugTaken = Boolean(
    effectiveSlug && existingTenants?.some((t) => t.slug === effectiveSlug),
  );
  const slugMalformed = Boolean(effectiveSlug) && !/^[a-z0-9-]{2,}$/.test(effectiveSlug);
  const canSubmit =
    displayName.trim().length > 0 &&
    effectiveSlug.length > 0 &&
    !slugTaken &&
    !slugMalformed &&
    ownerUserId.trim().length > 0;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await createTenant.mutateAsync({
        slug: effectiveSlug,
        displayName: displayName.trim(),
        ownerUserId: ownerUserId.trim(),
      });
      toast.success(`Created ${displayName.trim()}`);
      setDisplayName("");
      setSlug("");
      setSlugTouched(false);
      setOwnerUserId("");
      onDone();
    } catch (err) {
      // The client-side duplicate check is a hint from an already-loaded list,
      // not the authority -- the backend's 409 is, and it wins if they disagree
      // (another operator creating the same slug concurrently, for one).
      toast.error(
        isApiError(err) && err.status === 409
          ? `The slug "${effectiveSlug}" is already taken.`
          : isApiError(err)
            ? err.message
            : "Couldn't create the tenant.",
      );
    }
  }

  return (
    <DialogContent className="sm:max-w-lg">
      <form onSubmit={onSubmit}>
        <DialogHeader>
          <DialogTitle>New tenant</DialogTitle>
          <DialogDescription>
            The owner gets an active membership and the tenant owner role automatically.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-4">
          <div>
            <Label htmlFor="display_name">Organization name</Label>
            <Input
              id="display_name"
              autoFocus
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Acme Corporation"
              className="mt-1.5"
            />
          </div>

          <div>
            <div className="flex items-baseline justify-between">
              <Label htmlFor="slug">URL slug</Label>
              {slugTouched && (
                <button
                  type="button"
                  onClick={() => {
                    setSlugTouched(false);
                    setSlug("");
                  }}
                  className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                >
                  Reset to auto
                </button>
              )}
            </div>
            <Input
              id="slug"
              value={effectiveSlug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(slugify(e.target.value));
              }}
              placeholder="acme-corporation"
              className="mt-1.5 font-mono"
              aria-invalid={slugTaken || slugMalformed}
            />
            {slugTaken ? (
              <FieldError message={`"${effectiveSlug}" is already in use by another tenant.`} />
            ) : slugMalformed ? (
              <FieldError message="Use at least 2 characters: lowercase letters, numbers, and hyphens." />
            ) : (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                {effectiveSlug && !slugTouched && (
                  <>
                    <Check className="size-3 text-status-success" />
                    Derived from the name.
                  </>
                )}
                {effectiveSlug && slugTouched && <>Identifies the tenant in URLs and APIs.</>}
                {!effectiveSlug && <>Generated from the organization name as you type.</>}
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="owner_user_id">Owner</Label>
            <div className="mt-1.5">
              <UserPicker id="owner_user_id" value={ownerUserId} onChange={setOwnerUserId} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Becomes the tenant&apos;s first member, with the Tenant Owner role.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!canSubmit || createTenant.isPending}>
            {createTenant.isPending ? "Creating…" : "Create tenant"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
