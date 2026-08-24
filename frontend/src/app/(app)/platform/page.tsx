"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, Building2, Cpu, KeySquare, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status-badge";
import { SpendMeter } from "@/components/shared/spend-meter";
import { usePlatformOverview, usePlatformUsers, useTenants } from "@/features/platform/hooks";
import type { TenantSpend } from "@/lib/types";
import {
  usePlatformEffectivePermissions,
  usePlatformPermissionCatalog,
  usePlatformRoles,
} from "@/features/rbac/hooks";

export default function PlatformOverviewPage() {
  const tenants = useTenants();
  const users = usePlatformUsers({ limit: 5 });
  const roles = usePlatformRoles();
  const permissions = usePlatformPermissionCatalog();
  const mine = usePlatformEffectivePermissions();
  const overview = usePlatformOverview();

  const activeTenants = tenants.data?.filter((t) => t.status === "active").length;
  const suspendedTenants = tenants.data?.filter((t) => t.status === "suspended").length ?? 0;

  return (
    <div>
      <PageHeader
        eyebrow="Platform"
        title="Overview"
        description="The operator's view: every tenant, every account, and the platform-scope roles that govern them."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Building2}
          label="Tenants"
          value={tenants.data?.length}
          hint={
            tenants.data
              ? `${activeTenants} active${suspendedTenants > 0 ? `, ${suspendedTenants} suspended` : ""}`
              : undefined
          }
          href="/platform/tenants"
        />
        <StatCard
          icon={Users}
          label="Users"
          value={users.data?.total}
          hint="across all tenants"
          href="/platform/users"
        />
        <StatCard
          icon={ShieldCheck}
          label="Platform roles"
          value={roles.data?.length}
          hint="disjoint from tenant roles"
          href="/platform/roles"
        />
        <StatCard
          icon={KeySquare}
          label="Permissions"
          value={permissions.data?.length}
          hint="platform-scope catalog"
          href="/platform/permissions"
        />
      </div>

      {/* A 403 on the user directory is expected for an operator who holds
          tenant permissions but not platform.users.read -- ErrorState renders
          that case as "you don't have access", not as a failure. */}
      {users.error && (
        <div className="mt-6">
          <ErrorState error={users.error} resource="the user directory" scope="platform" />
        </div>
      )}

      <ProviderSpendSection overview={overview} />
      <TenantSpendSection overview={overview} />

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent tenants</CardTitle>
            <CardDescription>Newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            {tenants.isLoading && <Skeleton className="h-24 w-full" />}
            {tenants.error && <ErrorState error={tenants.error} resource="tenants" scope="platform" />}
            {tenants.data && tenants.data.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No tenants yet.{" "}
                <Link href="/platform/tenants" className="underline underline-offset-4">
                  Create the first one
                </Link>
                .
              </p>
            )}
            {tenants.data && tenants.data.length > 0 && (
              <ul className="divide-y divide-border">
                {tenants.data.slice(0, 6).map((tenant) => (
                  <li key={tenant.id} className="flex items-center justify-between py-2.5">
                    <div>
                      <p className="text-sm font-medium">{tenant.display_name}</p>
                      <p className="font-mono text-xs text-muted-foreground">{tenant.slug}</p>
                    </div>
                    <StatusBadge status={tenant.status} />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Your platform authority</CardTitle>
            <CardDescription>
              What this console will let you do, resolved from your platform roles.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {mine.isLoading && <Skeleton className="h-24 w-full" />}
            {mine.data && mine.data.permissions.length === 0 && (
              <p className="text-sm text-muted-foreground">
                You hold no platform permissions. Platform screens will refuse your requests —
                switch to a tenant from the top bar to do tenant-scope work.
              </p>
            )}
            {mine.data && mine.data.permissions.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {mine.data.permissions.map((code) => (
                  <Badge key={code} variant="secondary" className="font-mono text-xs">
                    {code}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  href,
}: {
  icon: LucideIcon;
  label: string;
  value: number | undefined;
  hint?: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40"
    >
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="size-4" />
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      {/* A div, not a p: `Skeleton` renders a div, and a div inside a p is
          invalid HTML that the browser reparents -- which shows up as a
          hydration mismatch rather than as a layout bug. */}
      <div className="mt-2 text-2xl font-semibold tabular-nums">
        {value ?? <Skeleton className="h-7 w-10" />}
      </div>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </Link>
  );
}

/** Provider totals: what the platform has committed, and what has been spent.
 *
 *  Budgets are summed **per grant**, not per configuration -- the counter is
 *  keyed by (tenant, model), so a 100k model granted to three tenants really
 *  can spend 300k, and showing 100k would understate the exposure. */
function ProviderSpendSection({
  overview,
}: {
  overview: ReturnType<typeof usePlatformOverview>;
}) {
  return (
    <section className="mt-6">
      <div className="mb-3 flex items-center gap-2">
        <Cpu className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">AI providers</h2>
      </div>

      {overview.isLoading && <Skeleton className="h-28 w-full" />}
      {overview.error && (
        <ErrorState error={overview.error} resource="provider spend" scope="platform" />
      )}
      {overview.data && overview.data.providers.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No model configurations yet.{" "}
          <Link href="/platform/model-configurations" className="underline underline-offset-4">
            Add one
          </Link>
          .
        </p>
      )}
      {overview.data && overview.data.providers.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {overview.data.providers.map((p) => (
            <SpendMeter
              key={p.provider}
              label={p.provider}
              used={p.used_tokens}
              limit={p.total_tokens}
              remaining={p.remaining_tokens}
              runningLow={p.running_low}
              note={
                p.has_unbudgeted
                  ? `${p.model_count} model(s) · some unbudgeted`
                  : `${p.model_count} model(s)`
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

/** Per-tenant tokens and messages, with a drill-down.
 *
 *  Tenants running low are returned first by the server, so the rows that need
 *  attention are visible without scrolling a long table. */
function TenantSpendSection({
  overview,
}: {
  overview: ReturnType<typeof usePlatformOverview>;
}) {
  const [detail, setDetail] = useState<TenantSpend | null>(null);
  const lowCount = overview.data?.tenants_running_low ?? 0;
  const threshold = Math.round((overview.data?.low_remaining_fraction ?? 0.1) * 100);

  return (
    <section className="mt-6">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Building2 className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Tenant usage</h2>
        </div>
        {lowCount > 0 && (
          <span className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1 text-xs font-medium text-destructive">
            <AlertTriangle className="size-3.5" />
            {lowCount} under {threshold}% remaining
          </span>
        )}
      </div>

      {overview.isLoading && <Skeleton className="h-32 w-full" />}
      {overview.data && overview.data.tenants.length > 0 && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Tenant</th>
                <th className="px-3 py-2 font-medium">Tokens this month</th>
                <th className="px-3 py-2 font-medium">Remaining</th>
                <th className="px-3 py-2 font-medium">Messages today</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {overview.data.tenants.map((t) => (
                <tr key={t.tenant_id} className={t.running_low ? "bg-destructive/5" : undefined}>
                  <td className="px-3 py-2">
                    {/* A button, not a link: this opens the breakdown in place.
                        The tenant admin screens are a different journey and are
                        reachable from Platform to Tenants. */}
                    <button
                      type="button"
                      onClick={() => setDetail(t)}
                      className="text-left font-medium underline-offset-4 hover:underline"
                    >
                      {t.display_name}
                    </button>
                    <p className="font-mono text-xs text-muted-foreground">{t.slug}</p>
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {t.used_tokens === null ? "?" : t.used_tokens.toLocaleString()}
                    <span className="text-muted-foreground">
                      {t.max_tokens_per_month === null
                        ? " / no limit"
                        : ` / ${t.max_tokens_per_month.toLocaleString()}`}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    <span className={t.running_low ? "font-medium text-destructive" : undefined}>
                      {t.remaining_tokens === null ? "—" : t.remaining_tokens.toLocaleString()}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    <button
                      type="button"
                      onClick={() => setDetail(t)}
                      className="underline-offset-4 hover:underline"
                    >
                      {t.used_messages_today === null
                        ? "?"
                        : t.used_messages_today.toLocaleString()}
                      <span className="text-muted-foreground">
                        {t.max_messages_per_day === null
                          ? " / no limit"
                          : ` / ${t.max_messages_per_day.toLocaleString()}`}
                      </span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="sm:max-w-2xl">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle>{detail.display_name}</DialogTitle>
                <DialogDescription className="font-mono text-xs">{detail.slug}</DialogDescription>
              </DialogHeader>

              <div className="grid gap-4 sm:grid-cols-2">
                <SpendMeter
                  label="Tokens this month"
                  used={detail.used_tokens}
                  limit={detail.max_tokens_per_month}
                  remaining={detail.remaining_tokens}
                  runningLow={detail.running_low}
                />
                <SpendMeter
                  label="Messages today"
                  unit="messages"
                  used={detail.used_messages_today}
                  limit={detail.max_messages_per_day}
                  remaining={detail.remaining_messages_today}
                />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-medium">By model</h3>
                {detail.models.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No model configurations granted to this tenant. Their answers use the
                    platform default, which has no per-model budget.
                  </p>
                ) : (
                  <ul className="divide-y rounded-lg border">
                    {detail.models.map((m) => (
                      <li
                        key={m.model_configuration_id}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-medium">{m.model_name}</p>
                          <p className="text-xs text-muted-foreground">{m.provider}</p>
                        </div>
                        <p className="shrink-0 tabular-nums">
                          {m.used_tokens === null ? "?" : m.used_tokens.toLocaleString()}
                          <span className="text-muted-foreground">
                            {m.token_budget_per_month === null
                              ? " / no limit"
                              : ` / ${m.token_budget_per_month.toLocaleString()}`}
                          </span>
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" render={<Link href="/platform/entitlements" />}>
                  Adjust limits
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
