"use client";

import { useMemo, useState } from "react";
import { Bot, Building2, Check, Copy, FileText, ShieldCheck, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import {
  useAssistantProfiles,
  useSetTenantAssistantProfile,
  useTenantAssistantPrompt,
} from "@/features/chatbot/hooks";
import { isApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type {
  AssistantProfileCode,
  AssistantProfileOption,
  TenantAssistantProfile,
} from "@/lib/types";

export default function AssistantProfilesPage() {
  const data = useAssistantProfiles();
  const [changing, setChanging] = useState<TenantAssistantProfile | null>(null);
  const [previewing, setPreviewing] = useState<TenantAssistantProfile | null>(null);

  const profiles = data.data?.profiles ?? [];
  const tenants = useMemo(() => data.data?.tenants ?? [], [data.data]);
  const counts = useMemo(() => {
    const byCode = new Map<string, number>();
    for (const t of tenants) byCode.set(t.profile, (byCode.get(t.profile) ?? 0) + 1);
    return byCode;
  }, [tenants]);

  return (
    <div>
      <PageHeader
        title="Assistant profiles"
        description="Which kind of organisation each tenant's AI assistant answers for. Every tenant starts as a UK nursery."
      />

      <Alert className="mb-6">
        <ShieldCheck className="size-4" />
        <AlertTitle>The safety core is the same on every profile</AlertTitle>
        <AlertDescription>
          Answering only from the tenant&rsquo;s sources, citations, prompt-injection defence,
          privacy and tenant isolation apply to all of them. A profile adds that sector&rsquo;s
          own rules and changes the default brief a tenant starts from. Anything a tenant wrote
          themselves is kept. Changes apply to the next answer and are recorded in the audit
          log. Tenants can see their profile but can&rsquo;t change it.
        </AlertDescription>
      </Alert>

      {data.isLoading && (
        <div className="mb-6 grid gap-3 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}
      {profiles.length > 0 && (
        <div className="mb-6 grid gap-3 md:grid-cols-3">
          {profiles.map((p) => (
            <Card key={p.code} size="sm">
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2 text-sm">
                  <span>{p.label}</span>
                  <Badge variant="secondary" className="tabular-nums">
                    {counts.get(p.code) ?? 0} {(counts.get(p.code) ?? 0) === 1 ? "tenant" : "tenants"}
                  </Badge>
                </CardTitle>
                <CardDescription className="text-xs">{p.summary}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}

      {data.isLoading && <TableSkeleton rows={4} columns={4} />}
      {data.error && <ErrorState error={data.error} resource="assistant profiles" scope="platform" />}

      {data.data && tenants.length === 0 && (
        <EmptyState
          icon={Building2}
          title="No tenants yet"
          description="Every new tenant starts on the UK nursery profile."
        />
      )}

      {tenants.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tenant</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Assistant profile</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tenants.map((t) => (
                    <TableRow key={t.tenant_id}>
                      <TableCell>
                        <div className="font-medium">{t.display_name}</div>
                        <div className="text-xs text-muted-foreground">{t.slug}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={t.status === "active" ? "secondary" : "outline"} className="capitalize">
                          {t.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex items-center gap-1.5">
                          <Bot className="size-3.5 text-muted-foreground" aria-hidden />
                          {t.profile_label}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button variant="ghost" size="sm" onClick={() => setPreviewing(t)}>
                            <FileText className="size-3.5" /> View prompt
                          </Button>
                          <Button variant="outline" size="sm" onClick={() => setChanging(t)}>
                            Change
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      <ChangeProfileDialog
        tenant={changing}
        profiles={profiles}
        onClose={() => setChanging(null)}
        onViewPrompt={(t) => {
          setChanging(null);
          setPreviewing(t);
        }}
      />
      <PromptSheet tenant={previewing} onClose={() => setPreviewing(null)} />
    </div>
  );
}

function ChangeProfileDialog({
  tenant,
  profiles,
  onClose,
  onViewPrompt,
}: {
  tenant: TenantAssistantProfile | null;
  profiles: AssistantProfileOption[];
  onClose: () => void;
  onViewPrompt: (tenant: TenantAssistantProfile) => void;
}) {
  const save = useSetTenantAssistantProfile();
  // Keyed on the tenant, so reopening the dialog for another tenant starts
  // from that tenant's current profile rather than the last choice made.
  const [picked, setPicked] = useState<{ tenantId: string; code: AssistantProfileCode } | null>(
    null,
  );
  const selected = tenant && picked?.tenantId === tenant.tenant_id ? picked.code : tenant?.profile;
  const leavingNursery = tenant?.profile === "nursery" && selected !== "nursery";
  const unchanged = selected === tenant?.profile;

  async function handleSave() {
    if (!tenant || !selected) return;
    try {
      const result = await save.mutateAsync({ tenantId: tenant.tenant_id, profile: selected });
      toast.success(`${tenant.display_name} now answers as: ${result.profile_label}.`);
      onViewPrompt({ ...tenant, profile: result.profile, profile_label: result.profile_label });
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change the profile.");
    }
  }

  return (
    <Dialog open={Boolean(tenant)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{tenant?.display_name ?? "Tenant"}: assistant profile</DialogTitle>
          <DialogDescription>
            Choose the kind of organisation this tenant&rsquo;s assistant answers for.
          </DialogDescription>
        </DialogHeader>

        <div role="radiogroup" aria-label="Assistant profile" className="space-y-2">
          {profiles.map((p) => {
            const active = selected === p.code;
            return (
              <button
                key={p.code}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => tenant && setPicked({ tenantId: tenant.tenant_id, code: p.code })}
                className={cn(
                  "flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors",
                  "focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  active ? "border-primary bg-primary/5" : "hover:bg-muted/50",
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                    active ? "border-primary bg-primary text-primary-foreground" : "border-input",
                  )}
                  aria-hidden
                >
                  {active && <Check className="size-3" />}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {p.label}
                    {tenant?.profile === p.code && (
                      <Badge variant="outline" className="text-[10px]">
                        Current
                      </Badge>
                    )}
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{p.summary}</span>
                </span>
              </button>
            );
          })}
        </div>

        {leavingNursery && (
          <Alert>
            <TriangleAlert className="size-4" />
            <AlertTitle>The nursery rules will stop applying</AlertTitle>
            <AlertDescription>
              EYFS safeguarding, child-data, medical, SEND and custody rules are part of the
              nursery profile only. Choose another profile only for a tenant that is not a
              nursery.
            </AlertDescription>
          </Alert>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={unchanged || save.isPending}>
            {save.isPending ? "Saving…" : "Save profile"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PromptSheet({
  tenant,
  onClose,
}: {
  tenant: TenantAssistantProfile | null;
  onClose: () => void;
}) {
  const preview = useTenantAssistantPrompt(tenant?.tenant_id ?? null);
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!preview.data) return;
    try {
      await navigator.clipboard.writeText(preview.data.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy to the clipboard.");
    }
  }

  return (
    <Sheet open={Boolean(tenant)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="pr-12">
          <SheetTitle>{tenant?.display_name ?? "Tenant"}: what the assistant is told</SheetTitle>
          <SheetDescription>
            The exact instructions sent with every question, above the passages found in the
            knowledge base and the visitor&rsquo;s own words. Opening this is recorded in the
            audit log.
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4">
          {preview.isLoading && <Skeleton className="h-full min-h-64" />}
          {preview.error && (
            <ErrorState error={preview.error} resource="assistant prompt" scope="platform" />
          )}
          {preview.data && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{preview.data.profile_label}</Badge>
                <Badge variant="outline">
                  {preview.data.has_saved_settings ? "Tenant's own settings" : "Default settings"}
                </Badge>
                <Badge variant="outline">
                  {preview.data.handoff_available ? "Offers a person" : "No handoff available"}
                </Badge>
                <span className="text-xs text-muted-foreground tabular-nums">
                  ≈ {preview.data.estimated_tokens.toLocaleString()} tokens ·{" "}
                  {preview.data.characters.toLocaleString()} characters
                </span>
                <Button variant="outline" size="sm" className="ml-auto" onClick={copy}>
                  {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <pre className="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
                {preview.data.prompt}
              </pre>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
