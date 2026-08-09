"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Building2, Plus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status-badge";
import { IdentityChip } from "@/components/shared/identity-chip";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { useHasPlatformPermission } from "@/features/rbac/hooks";
import { useTenantStore } from "@/stores/tenant-store";

export default function SelectTenantPage() {
  const router = useRouter();
  const { data: memberships, isLoading, error } = useMyMemberships();
  const setCurrentTenant = useTenantStore((s) => s.setCurrentTenant);
  const canCreateTenants = useHasPlatformPermission("platform.tenants.create") === true;

  function open(tenantId: string) {
    setCurrentTenant(tenantId);
    router.push(`/tenant/${tenantId}/dashboard`);
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Choose a tenant"
        description="Pick the tenant you want to administer. You can switch at any time from the top bar."
      />

      {isLoading && <TableSkeleton rows={3} columns={1} />}
      {error && <ErrorState error={error} resource="your tenants" />}

      {memberships && memberships.length === 0 && (
        // A platform operator seeing "ask a platform administrator" is the
        // dead end this screen used to be. If they can create tenants, say so
        // and link them to the place that does it.
        <EmptyState
          icon={Building2}
          title={canCreateTenants ? "No tenants yet" : "You're not a member of any tenant yet"}
          description={
            canCreateTenants
              ? "Create the first tenant to start onboarding an organization. You'll be able to open it here once you're a member."
              : "Accept an invitation, or ask a platform administrator to add you to one."
          }
          action={
            canCreateTenants ? (
              <Button size="sm" render={<Link href="/platform/tenants" />}>
                <Plus />
                Create a tenant
              </Button>
            ) : undefined
          }
        />
      )}

      {memberships && memberships.length > 0 && (
        <div className="space-y-2">
          {memberships.map((membership) => {
            const isActive = membership.status === "active";
            return (
              <Card key={membership.membership_id}>
                <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="flex min-w-0 flex-col gap-1.5">
                    <div className="flex items-center gap-2">
                      <Building2 className="size-4 shrink-0 text-muted-foreground" />
                      <IdentityChip value={membership.tenant_id} label="tenant" truncate={false} />
                    </div>
                    <div className="flex items-center gap-2 pl-6">
                      <StatusBadge status={membership.status} />
                      {membership.is_default && (
                        <span className="text-xs text-muted-foreground">Default</span>
                      )}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    disabled={!isActive}
                    onClick={() => open(membership.tenant_id)}
                    title={isActive ? undefined : "This membership isn't active"}
                  >
                    Open
                    <ArrowRight />
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
