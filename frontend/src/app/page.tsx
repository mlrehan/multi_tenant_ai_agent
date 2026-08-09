"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ShieldHalf } from "lucide-react";
import { usePlatformEffectivePermissions } from "@/features/rbac/hooks";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { useTenantStore } from "@/stores/tenant-store";

/**
 * Sends the signed-in person to the most useful screen for who they actually
 * are, rather than always to the tenant picker.
 *
 * This used to be a server-side `redirect("/select-tenant")`, which is only
 * right for a tenant user. A platform administrator — including the very first
 * one, who by definition has no tenants yet — landed on an empty picker with no
 * navigation and a message telling them to ask a platform administrator for
 * help. Routing needs the caller's effective permissions, which are only
 * reachable through the authenticated proxy, so this is a client component.
 *
 * `proxy.ts` has already bounced unauthenticated visitors to /login, so
 * everyone reaching here has a session.
 */
export default function RootPage() {
  const router = useRouter();
  const platform = usePlatformEffectivePermissions();
  const memberships = useMyMemberships();
  const storedTenantId = useTenantStore((s) => s.currentTenantId);

  const platformReady = platform.isSuccess || platform.isError;
  const membershipsReady = memberships.isSuccess || memberships.isError;
  const platformPermissionCount = platform.data?.permissions.length ?? 0;
  const membershipData = memberships.data;

  useEffect(() => {
    if (!platformReady || !membershipsReady) return;

    // A platform operator's home is the platform overview: it works with zero
    // tenants and is where they'd go to create the first one.
    if (platformPermissionCount > 0) {
      router.replace("/platform");
      return;
    }

    const active = (membershipData ?? []).filter((m) => m.status === "active");
    const remembered = active.find((m) => m.tenant_id === storedTenantId);
    const preferred = remembered ?? active.find((m) => m.is_default) ?? active[0];

    // One tenant is not a choice -- skip the picker entirely.
    if (active.length === 1 && preferred) {
      router.replace(`/tenant/${preferred.tenant_id}/dashboard`);
      return;
    }
    router.replace("/select-tenant");
  }, [
    platformReady,
    membershipsReady,
    platformPermissionCount,
    membershipData,
    storedTenantId,
    router,
  ]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-muted-foreground">
      <ShieldHalf className="size-8 animate-pulse text-primary" />
      <p className="text-sm">Loading your workspace…</p>
    </div>
  );
}
