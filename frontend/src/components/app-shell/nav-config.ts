import type { LucideIcon } from "lucide-react";
import {
  Building2,
  Fingerprint,
  Gauge,
  KeyRound,
  KeySquare,
  LayoutGrid,
  MessagesSquare,
  ShieldCheck,
  ShieldQuestion,
  Sparkles,
  UserCircle,
  Users,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Platform-scope permission required to show this item; omitted = always visible to a platform user. */
  requiresPlatformPermission?: string;
  /** Tenant-scope permission required; omitted = always visible to any active member. */
  requiresTenantPermission?: string;
}

export const platformNavItems: NavItem[] = [
  { label: "Overview", href: "/platform", icon: Gauge },
  { label: "Tenants", href: "/platform/tenants", icon: Building2 },
  {
    label: "Users",
    href: "/platform/users",
    icon: Users,
    requiresPlatformPermission: "platform.users.read",
  },
  { label: "Platform roles", href: "/platform/roles", icon: ShieldCheck },
  { label: "Permissions", href: "/platform/permissions", icon: KeyRound },
  {
    label: "Impersonation",
    href: "/platform/impersonation",
    icon: ShieldQuestion,
    requiresPlatformPermission: "platform.support.impersonate",
  },
];

/** Identity/account items are scope-independent -- they're about the signed-in
 * person, not about a tenant or the platform, so they render in every context. */
export const accountNavItems: NavItem[] = [
  { label: "My identity", href: "/account", icon: UserCircle },
];

export function tenantNavItems(tenantId: string): NavItem[] {
  return [
    { label: "Dashboard", href: `/tenant/${tenantId}/dashboard`, icon: Gauge },
    {
      label: "Members",
      href: `/tenant/${tenantId}/members`,
      icon: Users,
      requiresTenantPermission: "tenant.users.manage",
    },
    {
      label: "Roles & permissions",
      href: `/tenant/${tenantId}/rbac`,
      icon: KeySquare,
      requiresTenantPermission: "tenant.roles.manage",
    },
    {
      label: "Assistants",
      href: `/tenant/${tenantId}/assistants`,
      icon: Sparkles,
    },
    {
      label: "Knowledge bases",
      href: `/tenant/${tenantId}/knowledge-bases`,
      icon: LayoutGrid,
    },
    {
      label: "Conversations",
      href: `/tenant/${tenantId}/conversations`,
      icon: MessagesSquare,
    },
    {
      label: "Provider credentials",
      href: `/tenant/${tenantId}/credentials`,
      icon: Fingerprint,
      requiresTenantPermission: "tenant.provider_credentials.manage",
    },
  ];
}
