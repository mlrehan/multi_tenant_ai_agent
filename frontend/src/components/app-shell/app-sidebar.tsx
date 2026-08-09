"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldHalf } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  accountNavItems,
  platformNavItems,
  tenantNavItems,
  type NavItem,
} from "@/components/app-shell/nav-config";
import {
  usePlatformEffectivePermissions,
  useTenantEffectivePermissions,
} from "@/features/rbac/hooks";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { useTenantStore } from "@/stores/tenant-store";
import { extractTenantIdFromPath } from "@/lib/route-tenant";

function filterByPermission(
  items: NavItem[],
  platformPermissions: Set<string> | null,
  tenantPermissions: Set<string> | null,
): NavItem[] {
  return items.filter((item) => {
    if (item.requiresPlatformPermission) {
      // Unknown (still loading) -> hide rather than flash a forbidden item.
      if (!platformPermissions) return false;
      return platformPermissions.has(item.requiresPlatformPermission);
    }
    if (item.requiresTenantPermission) {
      if (!tenantPermissions) return false;
      return tenantPermissions.has(item.requiresTenantPermission);
    }
    return true;
  });
}

function NavLink({ item, pathname }: { item: NavItem; pathname: string }) {
  // `/platform` is a real page *and* the prefix of every other platform route,
  // so a prefix match would light up Overview on every screen in the section.
  // Section indexes match exactly; everything else matches by prefix so a
  // detail route still highlights its parent nav item.
  const segments = item.href.split("/").filter(Boolean);
  const isSectionIndex = segments.length <= 1;
  const isActive = isSectionIndex
    ? pathname === item.href
    : pathname === item.href || pathname.startsWith(`${item.href}/`);

  return (
    <SidebarMenuItem>
      <SidebarMenuButton isActive={isActive} tooltip={item.label} render={<Link href={item.href} />}>
        <item.icon />
        <span>{item.label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

/**
 * The sidebar shows what the signed-in person *can do*, not merely where they
 * currently are.
 *
 * It used to swap wholesale between a platform list and a tenant list based on
 * the URL prefix, which meant `/select-tenant` and `/account` — neither of
 * which is under `/platform` or `/tenant` — rendered an empty rail. A platform
 * administrator with no tenants yet therefore landed on a screen with no
 * navigation and no way forward. Both scopes now render whenever the caller
 * actually has them.
 */
export function AppSidebar() {
  const pathname = usePathname();
  const routeTenantId = extractTenantIdFromPath(pathname);
  const storedTenantId = useTenantStore((s) => s.currentTenantId);
  const { data: memberships } = useMyMemberships();

  // Fall back to the remembered tenant, then to the caller's default/first
  // active membership, so the tenant section stays reachable from screens that
  // aren't themselves tenant-scoped.
  const activeMemberships = memberships?.filter((m) => m.status === "active") ?? [];
  const fallbackTenantId =
    storedTenantId ??
    activeMemberships.find((m) => m.is_default)?.tenant_id ??
    activeMemberships[0]?.tenant_id ??
    null;
  const tenantId = routeTenantId ?? fallbackTenantId;

  const { data: platformData } = usePlatformEffectivePermissions();
  const { data: tenantData } = useTenantEffectivePermissions(tenantId);

  const platformPermissions = platformData ? new Set(platformData.permissions) : null;
  const tenantPermissions = tenantData ? new Set(tenantData.permissions) : null;

  const platformItems = filterByPermission(
    platformNavItems,
    platformPermissions,
    tenantPermissions,
  );
  const tenantItems = tenantId
    ? filterByPermission(tenantNavItems(tenantId), platformPermissions, tenantPermissions)
    : [];

  // Hide the whole platform section for a tenant-only user rather than showing
  // a heading over items they'd only be refused on.
  const showPlatform = platformPermissions !== null && platformPermissions.size > 0;

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-3 py-3">
        <Link href="/" className="flex items-center gap-2 px-1">
          <ShieldHalf className="size-5 shrink-0 text-primary" />
          <span className="truncate text-sm font-semibold tracking-tight group-data-[collapsible=icon]:hidden">
            IAM Control Center
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        {showPlatform && (
          <SidebarGroup>
            <SidebarGroupLabel>Platform</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {platformItems.map((item) => (
                  <NavLink key={item.href} item={item} pathname={pathname} />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        {tenantItems.length > 0 && (
          <SidebarGroup>
            <SidebarGroupLabel>Tenant</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {tenantItems.map((item) => (
                  <NavLink key={item.href} item={item} pathname={pathname} />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        <SidebarGroup>
          <SidebarGroupLabel>Account</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {accountNavItems.map((item) => (
                <NavLink key={item.href} item={item} pathname={pathname} />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
