"use client";

import { useState } from "react";
import { Archive, ArchiveRestore, Cpu, Pencil, Plus, X } from "lucide-react";
import { toast } from "sonner";
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
import { FieldError } from "@/components/shared/field-error";
import { TenantPicker } from "@/components/shared/tenant-picker";
import {
  useArchiveModelConfiguration,
  useCreateModelConfiguration,
  useGrantModelConfiguration,
  usePlatformModelConfigurations,
  useRestoreModelConfiguration,
  useRevokeModelConfiguration,
  useTenants,
  useUpdateModelConfiguration,
} from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import type { PlatformModelConfiguration } from "@/lib/types";

export default function ModelConfigurationsPage() {
  const { data, isLoading, error } = usePlatformModelConfigurations();
  const [createOpen, setCreateOpen] = useState(false);

  const configurations = data?.model_configurations;

  return (
    <div>
      <PageHeader
        title="Model configurations"
        description="The models this platform offers, and which tenants may use each one. Tenants select from what you make available here; they cannot create or edit these."
        actions={
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              New configuration
            </DialogTrigger>
            <CreateDialog onDone={() => setCreateOpen(false)} />
          </Dialog>
        }
      />

      {isLoading && <TableSkeleton rows={3} columns={4} />}
      {error && <ErrorState error={error} resource="model configurations" />}

      {configurations && configurations.length === 0 && (
        <EmptyState
          icon={Cpu}
          title="No model configurations yet"
          description="Create one, then make it available to the tenants that should be able to use it. Until a tenant has at least one, its administrators cannot create an assistant."
          action={
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus />
              New configuration
            </Button>
          }
        />
      )}

      {configurations && configurations.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead>Available to</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {configurations.map((c) => (
                  <ConfigurationRow key={c.id} configuration={c} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ConfigurationRow({ configuration }: { configuration: PlatformModelConfiguration }) {
  const { data: tenantData } = useTenants();
  const archive = useArchiveModelConfiguration();
  const restore = useRestoreModelConfiguration();
  const revoke = useRevokeModelConfiguration();
  const [editOpen, setEditOpen] = useState(false);
  const [grantOpen, setGrantOpen] = useState(false);

  const tenantName = new Map((tenantData ?? []).map((t) => [t.id, t.display_name]));

  async function handleArchiveToggle() {
    const mutation = configuration.archived_at ? restore : archive;
    try {
      await mutation.mutateAsync(configuration.id);
      toast.success(
        configuration.archived_at
          ? `${configuration.model_name} is available again.`
          : `${configuration.model_name} archived — existing assistants keep working.`,
      );
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change this configuration.");
    }
  }

  async function handleRevoke(tenantId: string) {
    try {
      await revoke.mutateAsync({ id: configuration.id, tenantId });
      toast.success("Access revoked.");
    } catch (err) {
      // A 409 here is the useful case: the tenant still has assistants using
      // it, and the message says how many.
      toast.error(isApiError(err) ? err.message : "Couldn't revoke access.");
    }
  }

  return (
    <TableRow>
      <TableCell className="align-top">
        <div className="font-medium">{configuration.model_name}</div>
        {configuration.owning_tenant_id && (
          <p className="mt-0.5 text-xs whitespace-normal text-muted-foreground">
            Owned by a single tenant (created before availability was managed here).
          </p>
        )}
        {configuration.token_budget_per_month !== null && (
          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
            {configuration.token_budget_per_month.toLocaleString()} tokens/month
          </p>
        )}
      </TableCell>
      <TableCell className="align-top">
        {configuration.tenant_ids.length === 0 ? (
          <span className="text-xs text-muted-foreground">No tenants yet</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {configuration.tenant_ids.map((tenantId) => (
              <Badge key={tenantId} variant="secondary" className="gap-1">
                {tenantName.get(tenantId) ?? tenantId.slice(0, 8)}
                <button
                  type="button"
                  aria-label={`Revoke from ${tenantName.get(tenantId) ?? tenantId}`}
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => void handleRevoke(tenantId)}
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </div>
        )}
      </TableCell>
      <TableCell className="align-top">
        <Badge variant={configuration.archived_at ? "outline" : "secondary"}>
          {configuration.archived_at ? "Archived" : "Active"}
        </Badge>
      </TableCell>
      <TableCell className="align-top text-right">
        <div className="flex justify-end gap-2">
          <Dialog open={grantOpen} onOpenChange={setGrantOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <Plus />
              Tenant
            </DialogTrigger>
            <GrantDialog configuration={configuration} onDone={() => setGrantOpen(false)} />
          </Dialog>
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger render={<Button size="xs" variant="outline" />}>
              <Pencil />
              Edit
            </DialogTrigger>
            <EditDialog configuration={configuration} onDone={() => setEditOpen(false)} />
          </Dialog>
          <Button
            size="xs"
            variant="ghost"
            aria-label={
              configuration.archived_at
                ? `Restore ${configuration.model_name}`
                : `Archive ${configuration.model_name}`
            }
            onClick={() => void handleArchiveToggle()}
          >
            {configuration.archived_at ? <ArchiveRestore /> : <Archive />}
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function CreateDialog({ onDone }: { onDone: () => void }) {
  const create = useCreateModelConfiguration();
  const [modelName, setModelName] = useState("");
  const [budget, setBudget] = useState("");
  const [error, setError] = useState<string>();

  async function handleSubmit() {
    if (!modelName.trim()) {
      setError("Enter a model name.");
      return;
    }
    try {
      await create.mutateAsync({
        model_name: modelName.trim(),
        token_budget_per_month: budget ? Number(budget) : null,
      });
      toast.success(`${modelName.trim()} added. Make it available to a tenant to put it to use.`);
      onDone();
    } catch (err) {
      setError(isApiError(err) ? err.message : "Couldn't create this configuration.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New model configuration</DialogTitle>
        <DialogDescription>
          Creating it makes it available to nobody. Grant it to a tenant afterwards to let their
          administrators choose it for an assistant.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-2">
        <div>
          <Label htmlFor="model-name">Model name</Label>
          <Input
            id="model-name"
            className="mt-1.5"
            placeholder="claude-opus-5"
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          />
          <FieldError message={error} />
        </div>
        <div>
          <Label htmlFor="budget">Monthly token budget (optional)</Label>
          <Input
            id="budget"
            className="mt-1.5"
            type="number"
            min={0}
            placeholder="Leave blank for no cap"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button disabled={create.isPending} onClick={() => void handleSubmit()}>
          {create.isPending ? "Creating…" : "Create"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function EditDialog({
  configuration,
  onDone,
}: {
  configuration: PlatformModelConfiguration;
  onDone: () => void;
}) {
  const update = useUpdateModelConfiguration();
  const [modelName, setModelName] = useState(configuration.model_name);
  const [budget, setBudget] = useState(
    configuration.token_budget_per_month === null
      ? ""
      : String(configuration.token_budget_per_month),
  );
  const [error, setError] = useState<string>();

  async function handleSubmit() {
    if (!modelName.trim()) {
      setError("Enter a model name.");
      return;
    }
    try {
      await update.mutateAsync({
        id: configuration.id,
        model_name: modelName.trim(),
        token_budget_per_month: budget ? Number(budget) : null,
      });
      toast.success("Configuration updated.");
      onDone();
    } catch (err) {
      setError(isApiError(err) ? err.message : "Couldn't update this configuration.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Edit {configuration.model_name}</DialogTitle>
        <DialogDescription>
          Changes apply to every assistant already using this configuration.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-2">
        <div>
          <Label htmlFor="edit-model-name">Model name</Label>
          <Input
            id="edit-model-name"
            className="mt-1.5"
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          />
          <FieldError message={error} />
        </div>
        <div>
          <Label htmlFor="edit-budget">Monthly token budget (optional)</Label>
          <Input
            id="edit-budget"
            className="mt-1.5"
            type="number"
            min={0}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button disabled={update.isPending} onClick={() => void handleSubmit()}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function GrantDialog({
  configuration,
  onDone,
}: {
  configuration: PlatformModelConfiguration;
  onDone: () => void;
}) {
  const grant = useGrantModelConfiguration();
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState<string>();

  async function handleSubmit() {
    if (!tenantId) {
      setError("Choose a tenant.");
      return;
    }
    try {
      await grant.mutateAsync({ id: configuration.id, tenantId });
      toast.success("This tenant can now use the configuration.");
      onDone();
    } catch (err) {
      setError(isApiError(err) ? err.message : "Couldn't grant access.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Make {configuration.model_name} available</DialogTitle>
        <DialogDescription>
          The tenant&rsquo;s administrators will be able to choose this model when they create or
          edit an assistant.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-2">
        <div>
          <Label htmlFor="grant-tenant">Tenant</Label>
          <div className="mt-1.5">
            <TenantPicker value={tenantId} onChange={setTenantId} />
          </div>
          <FieldError message={error} />
        </div>
      </div>
      <DialogFooter>
        <Button disabled={grant.isPending} onClick={() => void handleSubmit()}>
          {grant.isPending ? "Granting…" : "Grant access"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
