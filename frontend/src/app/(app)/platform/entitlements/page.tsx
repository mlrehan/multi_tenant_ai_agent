"use client";

import { useMemo, useState } from "react";
import { Building2, Cpu, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { IdentityChip } from "@/components/shared/identity-chip";
import {
  useAiProviders,
  useSetTenantEntitlements,
  useTenantEntitlements,
} from "@/features/chatbot/hooks";
import { useTenants } from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import type { TenantEntitlements } from "@/lib/types";

/** The capability flags, kept as data so the form and the table cannot drift
 *  apart. `allow_own_provider_credentials` and `allow_create_assistant` were
 *  removed with the features they gated (migration f1c94a70b2d8) -- a toggle
 *  that governs nothing is worse than an absent one, because an operator sets
 *  it and believes they have changed something. */
const CAPABILITIES = [
  { key: "allow_invite_members", label: "Invite members", hint: "Add people to the tenant" },
  { key: "allow_create_roles", label: "Create roles", hint: "Define custom tenant roles" },
] as const;

const LIMITS = [
  { key: "max_knowledge_bases", label: "Knowledge bases" },
  { key: "max_chat_widgets", label: "Chat widgets" },
  { key: "max_messages_per_day", label: "AI messages / day" },
  { key: "max_tokens_per_month", label: "Tokens / month" },
] as const;

export default function EntitlementsPage() {
  const entitlements = useTenantEntitlements();
  const providers = useAiProviders();
  const tenants = useTenants();
  const [editing, setEditing] = useState<TenantEntitlements | null>(null);

  const nameFor = useMemo(() => {
    const byId = new Map((tenants.data ?? []).map((t) => [t.id, t.display_name]));
    return (id: string) => byId.get(id) ?? null;
  }, [tenants.data]);

  return (
    <div>
      <PageHeader
        title="Tenant entitlements"
        description="What each tenant may have, and how much it may spend. Limits govern creation only — lowering one never disables resources a tenant already has."
      />

      <Alert className="mb-6">
        <ShieldCheck className="size-4" />
        <AlertTitle>Blank means unlimited</AlertTitle>
        <AlertDescription>
          An empty limit is uncapped. <strong>Zero is different</strong> — it means none at
          all. Tenants can read their own plan but cannot change it; the table grants them
          SELECT only.
        </AlertDescription>
      </Alert>

      {entitlements.isLoading && <TableSkeleton rows={4} columns={6} />}
      {entitlements.error && (
        <ErrorState error={entitlements.error} resource="tenant entitlements" />
      )}

      {entitlements.data?.entitlements.length === 0 && (
        <EmptyState
          icon={Building2}
          title="No tenants yet"
          description="Every tenant is listed here as soon as one exists — on the platform defaults until you set a plan."
        />
      )}

      {entitlements.data && entitlements.data.entitlements.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tenant</TableHead>
                  {LIMITS.map((l) => (
                    <TableHead key={l.key} className="text-right">
                      {l.label}
                    </TableHead>
                  ))}
                  <TableHead>Capabilities</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entitlements.data.entitlements.map((e) => (
                  <TableRow key={e.tenant_id}>
                    <TableCell>
                      <div className="font-medium">{nameFor(e.tenant_id) ?? "Tenant"}</div>
                      <IdentityChip value={e.tenant_id} label="tenant" />
                    </TableCell>
                    {LIMITS.map((l) => {
                      const limit = e[l.key];
                      return (
                        <TableCell key={l.key} className="text-right tabular-nums">
                          {limit === null ? (
                            <span className="text-muted-foreground">∞</span>
                          ) : (
                            limit.toLocaleString()
                          )}
                        </TableCell>
                      );
                    })}
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {CAPABILITIES.filter((c) => e[c.key]).map((c) => (
                          <Badge key={c.key} variant="secondary" className="text-xs">
                            {c.label}
                          </Badge>
                        ))}
                        {CAPABILITIES.every((c) => !e[c.key]) && (
                          <span className="text-sm text-muted-foreground">None granted</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" onClick={() => setEditing(e)}>
                        Edit plan
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card className="mt-8">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Cpu className="size-4" /> AI providers
          </CardTitle>
          <CardDescription>
            Which providers this deployment can actually talk to. Unsupported ones are shown
            rather than hidden — configuring one is refused server-side, so the answer to
            &ldquo;why can&rsquo;t I pick this?&rdquo; is visible here.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {providers.data?.providers.map((p) => (
              <div
                key={p.provider}
                className={`rounded-lg border p-3 ${p.supported ? "" : "opacity-60"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{p.label}</span>
                  <Badge variant={p.supported ? "default" : "outline"}>
                    {p.supported ? "Available" : "Not implemented"}
                  </Badge>
                </div>
                <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                  <li>Embeddings: {p.supports_embeddings ? "yes" : "no"}</li>
                  <li>Custom dimensions: {p.supports_embedding_dimensions ? "yes" : "no"}</li>
                  <li>Reasoning effort: {p.supports_reasoning_effort ? "yes" : "no"}</li>
                </ul>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <EditPlanDialog
        entitlements={editing}
        tenantName={editing ? nameFor(editing.tenant_id) : null}
        onClose={() => setEditing(null)}
      />
    </div>
  );
}

function EditPlanDialog({
  entitlements,
  tenantName,
  onClose,
}: {
  entitlements: TenantEntitlements | null;
  tenantName: string | null;
  onClose: () => void;
}) {
  const save = useSetTenantEntitlements();
  // Limits are held as strings so "" can mean unlimited and "0" can mean none —
  // a number-typed state would collapse both to the same falsy value, which is
  // exactly the distinction this whole screen rests on.
  const [limits, setLimits] = useState<Record<string, string>>({});
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);

  if (entitlements && !dirty) {
    const nextLimits = Object.fromEntries(
      LIMITS.map((l) => [l.key, entitlements[l.key]?.toString() ?? ""]),
    );
    const nextFlags = Object.fromEntries(
      CAPABILITIES.map((c) => [c.key, entitlements[c.key]]),
    );
    if (JSON.stringify(nextLimits) !== JSON.stringify(limits)) {
      setLimits(nextLimits);
      setFlags(nextFlags);
    }
  }

  async function handleSave() {
    if (!entitlements) return;
    try {
      await save.mutateAsync({
        tenantId: entitlements.tenant_id,
        body: {
          max_knowledge_bases: parseLimit(limits.max_knowledge_bases),
          max_chat_widgets: parseLimit(limits.max_chat_widgets),
          max_messages_per_day: parseLimit(limits.max_messages_per_day),
          max_tokens_per_month: parseLimit(limits.max_tokens_per_month),
          allow_invite_members: flags.allow_invite_members,
          allow_create_roles: flags.allow_create_roles,
        },
      });
      toast.success("Plan updated.");
      setDirty(false);
      onClose();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't update this plan.");
    }
  }

  return (
    <Dialog open={Boolean(entitlements)} onOpenChange={(open) => !open && (setDirty(false), onClose())}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{tenantName ?? "Tenant"} — plan</DialogTitle>
          <DialogDescription>
            Leave a limit blank for unlimited. Withdrawing a capability stops new resources
            being created; anything already built keeps working.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {LIMITS.map((l) => (
              <div key={l.key}>
                <Label htmlFor={l.key}>{l.label}</Label>
                <Input
                  id={l.key}
                  className="mt-1.5"
                  inputMode="numeric"
                  placeholder="Unlimited"
                  value={limits[l.key] ?? ""}
                  onChange={(e) => {
                    setDirty(true);
                    setLimits((s) => ({ ...s, [l.key]: e.target.value.replace(/[^\d]/g, "") }));
                  }}
                />
              </div>
            ))}
          </div>

          <div className="space-y-2 rounded-lg border p-3">
            {CAPABILITIES.map((c) => (
              <div key={c.key} className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm font-medium">{c.label}</div>
                  <div className="text-xs text-muted-foreground">{c.hint}</div>
                </div>
                <Switch
                  checked={flags[c.key] ?? false}
                  onCheckedChange={(v) => {
                    setDirty(true);
                    setFlags((s) => ({ ...s, [c.key]: v }));
                  }}
                />
              </div>
            ))}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => (setDirty(false), onClose())}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "" => unlimited (null), "0" => a real limit of none. Collapsing the two is
 *  the bug this function exists to avoid. */
function parseLimit(value: string | undefined): number | null {
  if (value === undefined || value.trim() === "") return null;
  const n = Number.parseInt(value, 10);
  return Number.isNaN(n) ? null : n;
}
