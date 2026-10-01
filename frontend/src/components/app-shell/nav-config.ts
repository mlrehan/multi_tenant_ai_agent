import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  BookOpen,
  Bot,
  Building2,
  CircleDollarSign,
  Cpu,
  Inbox,
  Gauge,
  KeyRound,
  KeySquare,
  LayoutGrid,
  MessagesSquare,
  ShieldCheck,
  ShieldQuestion,
  SlidersHorizontal,
  ThumbsUp,
  UserCircle,
  Users,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Opens in a new tab. Set for destinations outside this console's app
   *  screens (another tool, or the standalone help guide): leaving the app in
   *  the same tab would drop whatever the person was doing, and such a page is
   *  somewhere they return *from*, not navigate *to*. */
  external?: boolean;
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
  {
    label: "Model configurations",
    href: "/platform/model-configurations",
    icon: Cpu,
    requiresPlatformPermission: "platform.model_configurations.manage",
  },
  {
    label: "AI prices",
    href: "/platform/model-prices",
    icon: CircleDollarSign,
    requiresPlatformPermission: "platform.model_configurations.manage",
  },
  {
    label: "Tenant entitlements",
    href: "/platform/entitlements",
    icon: SlidersHorizontal,
    // Same permission as the model catalogue: both are the platform deciding
    // what a tenant may spend the platform's money on.
    requiresPlatformPermission: "platform.model_configurations.manage",
  },
  {
    label: "Assistant profiles",
    href: "/platform/assistant-profiles",
    icon: Bot,
    // The same authority as entitlements: deciding how a tenant's assistant
    // behaves, as well as what it may spend.
    requiresPlatformPermission: "platform.model_configurations.manage",
  },
  {
    label: "Answer feedback",
    href: "/platform/feedback",
    icon: ThumbsUp,
    // The operator dashboard's permission: judging whether answers are any
    // good is the same job as watching what they cost.
    requiresPlatformPermission: "platform.model_configurations.manage",
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

/** Tools that live outside this console.
 *
 *  Its own group rather than an entry in Account, because it is neither about
 *  the signed-in person nor part of this application -- and because the group
 *  label is what tells someone the link will take them elsewhere before they
 *  click it. */
export const dataAnalysisNavItems: NavItem[] = [
  {
    label: "Nursery analytics",
    href: "https://nursery.falgoon.co.uk/",
    icon: BarChart3,
    external: true,
  },
];

/** The tenant administrator guide, served by this console at a public
 *  address (see `next.config.ts`) so the same link can be emailed to someone
 *  without an account. New tab: it is a long document read beside the
 *  console, not a screen that replaces it. */
export const HELP_GUIDE_PATH = "/help";

export const helpNavItems: NavItem[] = [
  { label: "User guide", href: HELP_GUIDE_PATH, icon: BookOpen, external: true },
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
      label: "AI Chatbot",
      href: `/tenant/${tenantId}/chatbot`,
      icon: Bot,
    },
    {
      label: "Inbox",
      href: `/tenant/${tenantId}/inbox`,
      icon: Inbox,
      requiresTenantPermission: "tenant.conversations.view",
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
      label: "Answer feedback",
      href: `/tenant/${tenantId}/feedback`,
      icon: ThumbsUp,
      // Ratings carry visitors' questions -- the same permission as reading
      // every conversation.
      requiresTenantPermission: "tenant.conversations.view",
    },
  ];
}
