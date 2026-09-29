"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronsUpDown, LayoutDashboard, LogOut, ShieldHalf, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useMyMemberships } from "@/features/tenancy/hooks";
import { tenantLabel } from "@/features/tenancy/api";
import { usePlatformEffectivePermissions } from "@/features/rbac/hooks";
import { useLogout, useMyAccount } from "@/features/auth/hooks";
import { useTenantStore } from "@/stores/tenant-store";
import { extractTenantIdFromPath } from "@/lib/route-tenant";

/** "jane.doe@acme.test" -> "JD"; "admin@lait.co.uk" -> "AD". Derived from the
 *  address because that is the one identity field every account has. */
function initialsFor(email: string | undefined): string {
  if (!email) return "";
  const local = email.split("@")[0] ?? "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}

export function Topbar() {
  const router = useRouter();
  const tenantId = extractTenantIdFromPath(usePathname());
  const { data: memberships } = useMyMemberships();
  const logout = useLogout();
  // Who is signed in, always visible. A console that can suspend accounts and
  // impersonate users must never leave the operator unsure whose session it is.
  const { data: account } = useMyAccount();
  // Only offered to someone who can use it; a tenant admin following it would
  // land on a screen that refuses every request.
  const { data: platform } = usePlatformEffectivePermissions();
  const isOperator = (platform?.permissions.length ?? 0) > 0;
  const setCurrentTenant = useTenantStore((s) => s.setCurrentTenant);

  const current = memberships?.find((m) => m.tenant_id === tenantId);

  function switchTenant(nextTenantId: string) {
    setCurrentTenant(nextTenantId);
    router.push(`/tenant/${nextTenantId}/dashboard`);
  }

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
      <SidebarTrigger />

      <div className="flex-1" />

      {memberships && memberships.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="gap-2" />}>
            <ShieldHalf className="size-3.5 text-muted-foreground" />
            {/* Plain text, not an IdentityChip: the chip is itself a
                <button> (copy to clipboard), and nesting a button inside
                this trigger button is invalid HTML that React reports as a
                hydration error. The copyable chip lives on the page body
                instead, where it isn't inside an interactive ancestor. */}
            {current ? (
              <span className="max-w-[14rem] truncate text-sm">{tenantLabel(current)}</span>
            ) : tenantId ? (
              <span className="font-mono text-xs">
                {`${tenantId.slice(0, 8)}…${tenantId.slice(-4)}`}
              </span>
            ) : (
              <span className="text-muted-foreground">Select tenant</span>
            )}
            <ChevronsUpDown className="size-3.5 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            {/* The label and the tenants it names must sit inside one
                DropdownMenuGroup: Base UI's GroupLabel reads MenuGroupContext
                and throws "MenuGroupContext is missing" when rendered as a
                direct child of the content. "Manage tenants" stays outside --
                it's a separate action, not one of the listed tenants. */}
            <DropdownMenuGroup>
              <DropdownMenuLabel>Your tenants</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {memberships.map((m) => (
                <DropdownMenuItem
                  key={m.membership_id}
                  onClick={() => switchTenant(m.tenant_id)}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="min-w-0">
                    <span
                      className={
                        m.tenant_display_name
                          ? "block truncate text-sm"
                          : "block truncate font-mono text-xs"
                      }
                    >
                      {tenantLabel(m)}
                    </span>
                    {m.tenant_slug && (
                      <span className="block truncate font-mono text-[0.7rem] text-muted-foreground">
                        {m.tenant_slug}
                      </span>
                    )}
                  </span>
                  {/* Only unusual states are called out: "active" on every
                      row is noise that hides the one row that isn't. */}
                  {m.status !== "active" && (
                    <span className="shrink-0 text-xs text-muted-foreground capitalize">{m.status}</span>
                  )}
                  {m.tenant_id === tenantId && m.status === "active" && (
                    <span className="shrink-0 text-xs text-muted-foreground">Current</span>
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => router.push("/select-tenant")}>
              Manage tenants
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="sm" className="h-9 gap-2 rounded-full pr-2.5 pl-1" />}
          aria-label={account ? `Account menu for ${account.email}` : "Account menu"}
        >
          <Avatar className="size-7">
            <AvatarFallback className="bg-primary text-[0.7rem] font-semibold text-primary-foreground">
              {initialsFor(account?.email)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden max-w-[16rem] truncate text-sm md:inline">{account?.email}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          {account && (
            <>
              <DropdownMenuGroup>
                <DropdownMenuLabel className="font-normal">
                  <span className="block text-xs text-muted-foreground">Signed in as</span>
                  <span className="block truncate text-sm font-medium text-foreground">
                    {account.email}
                  </span>
                </DropdownMenuLabel>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem render={<Link href="/account" />}>
            <UserRound className="size-3.5" />
            My account
          </DropdownMenuItem>
          {isOperator && (
            <DropdownMenuItem render={<Link href="/platform" />}>
              <LayoutDashboard className="size-3.5" />
              Platform overview
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            <LogOut className="size-3.5" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
