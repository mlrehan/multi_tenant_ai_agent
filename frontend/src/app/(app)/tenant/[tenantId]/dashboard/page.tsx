"use client";

import { use as usePromise } from "react";
import Link from "next/link";
import { ArrowRight, Database, KeySquare, Sparkles, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { IdentityChip } from "@/components/shared/identity-chip";
import { PermissionList } from "@/components/shared/permission-list";
import { useTenantEffectivePermissions, useTenantPermissionCatalog } from "@/features/rbac/hooks";
import { useTenantMembers } from "@/features/tenancy/hooks";
import { useAssistants, useKnowledgeBases } from "@/features/ai-resources/hooks";

export default function TenantDashboardPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = usePromise(params);

  const permissions = useTenantEffectivePermissions(tenantId);
  const catalog = useTenantPermissionCatalog(tenantId);
  const members = useTenantMembers(tenantId);
  const assistants = useAssistants(tenantId);
  const knowledgeBases = useKnowledgeBases(tenantId);

  const held = new Set(permissions.data?.permissions ?? []);
  const canManageMembers = held.has("tenant.users.manage");

  return (
    <div>
      <PageHeader
        title="Overview"
        description="What you can see and do in this tenant."
        actions={<IdentityChip value={tenantId} label="tenant" truncate={false} />}
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          icon={Users}
          label="Members"
          value={canManageMembers ? members.data?.length : undefined}
          hint={canManageMembers ? undefined : "Needs tenant.users.manage"}
          href={canManageMembers ? `/tenant/${tenantId}/members` : undefined}
          isLoading={canManageMembers && members.isLoading}
        />
        <StatTile
          icon={Sparkles}
          label="Assistants"
          value={assistants.data?.assistants.length}
          href={`/tenant/${tenantId}/assistants`}
          isLoading={assistants.isLoading}
        />
        <StatTile
          icon={Database}
          label="Knowledge bases"
          value={knowledgeBases.data?.knowledge_bases.length}
          href={`/tenant/${tenantId}/knowledge-bases`}
          isLoading={knowledgeBases.isLoading}
        />
        <StatTile
          icon={KeySquare}
          label="Your permissions"
          value={permissions.data?.permissions.length}
          href={`/tenant/${tenantId}/rbac`}
          isLoading={permissions.isLoading}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What you can do here</CardTitle>
          <CardDescription>
            Your effective permissions in this tenant, after roles, inheritance, and overrides are
            resolved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {permissions.isLoading && <Skeleton className="h-32 w-full" />}
          {permissions.data && catalog.data && (
            <PermissionList
              permissions={catalog.data.filter((p) => held.has(p.code))}
              emptyMessage="You hold no permissions in this tenant yet. Ask an administrator to assign you a role."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  href,
  isLoading,
}: {
  icon: LucideIcon;
  label: string;
  value?: number;
  hint?: string;
  href?: string;
  isLoading?: boolean;
}) {
  const body = (
    <CardContent className="py-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{label}</span>
        <Icon className="size-4 text-muted-foreground" />
      </div>
      <div className="mt-2 flex items-end justify-between gap-2">
        {isLoading ? (
          <Skeleton className="h-8 w-12" />
        ) : (
          <span className="text-2xl font-semibold tabular-nums">
            {value ?? <span className="text-base font-normal text-muted-foreground">—</span>}
          </span>
        )}
        {href && <ArrowRight className="size-3.5 text-muted-foreground" />}
      </div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </CardContent>
  );

  if (!href) return <Card>{body}</Card>;

  return (
    <Card className="transition-colors hover:border-primary/40">
      <Link href={href} className="block">
        {body}
      </Link>
    </Card>
  );
}
