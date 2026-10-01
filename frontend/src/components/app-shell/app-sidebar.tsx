"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Check, ExternalLink, Link2, ShieldHalf } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  accountNavItems,
  dataAnalysisNavItems,
  HELP_GUIDE_PATH,
  helpNavItems,
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

function NavLink({
  item,
  pathname,
  action,
}: {
  item: NavItem;
  pathname: string;
  /** A secondary button beside the link, such as "copy link". */
  action?: ReactNode;
}) {
  // `/platform` is a real page *and* the prefix of every other platform route,
  // so a prefix match would light up Overview on every screen in the section.
  // Section indexes match exactly; everything else matches by prefix so a
  // detail route still highlights its parent nav item.
  // An external destination is never "where you are", so it skips the active
  // matching entirely -- and uses a plain anchor rather than `next/link`,
  // which is for in-app navigation and would prefetch a third-party origin.
  if (item.external) {
    return (
      <SidebarMenuItem>
        <SidebarMenuButton
          tooltip={item.label}
          render={
            <a
              href={item.href}
              target="_blank"
              // `noreferrer` as well as `noopener`: the opened page has no
              // business knowing which console screen it was launched from.
              rel="noopener noreferrer"
            />
          }
        >
          <item.icon />
          <span>{item.label}</span>
          {!action && <ExternalLink className="ml-auto size-3 opacity-60" />}
        </SidebarMenuButton>
        {action}
      </SidebarMenuItem>
    );
  }

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
 * Copies the guide's full address, for pasting into an email or a chat.
 *
 * Built from the browser's own origin rather than a configured one: the
 * console has no public-URL setting (the backend origin is server-only by
 * design), and the origin the admin is using is, by definition, one that
 * reaches this console.
 */
function CopyHelpLinkAction() {
  const [copied, setCopied] = useState(false);

  async function copy() {
    const url = `${window.location.origin}${HELP_GUIDE_PATH}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success("Guide link copied", {
        description: `${url} -- anyone with the link can read it, no sign-in needed.`,
      });
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Couldn't copy the link", { description: url });
    }
  }

  return (
    <SidebarMenuAction onClick={copy} title="Copy a shareable link to the guide" aria-label="Copy guide link">
      {copied ? <Check /> : <Link2 />}
    </SidebarMenuAction>
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

        <SidebarGroup>
          <SidebarGroupLabel>Help</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {helpNavItems.map((item) => (
                <NavLink
                  key={item.href}
                  item={item}
                  pathname={pathname}
                  action={<CopyHelpLinkAction />}
                />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Data analysis</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {dataAnalysisNavItems.map((item) => (
                <NavLink key={item.href} item={item} pathname={pathname} />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
