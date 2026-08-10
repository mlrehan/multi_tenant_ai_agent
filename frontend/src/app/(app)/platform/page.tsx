"use client";

import Link from "next/link";
import { Building2, KeySquare, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status-badge";
import { usePlatformUsers, useTenants } from "@/features/platform/hooks";
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
