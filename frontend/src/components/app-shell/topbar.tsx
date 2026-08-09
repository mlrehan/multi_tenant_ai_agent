"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronsUpDown, LogOut, ShieldHalf } from "lucide-react";
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
import { useLogout } from "@/features/auth/hooks";
import { useTenantStore } from "@/stores/tenant-store";
import { extractTenantIdFromPath } from "@/lib/route-tenant";

export function Topbar() {
  const router = useRouter();
  const tenantId = extractTenantIdFromPath(usePathname());
  const { data: memberships } = useMyMemberships();
  const logout = useLogout();
  const setCurrentTenant = useTenantStore((s) => s.setCurrentTenant);

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
            {tenantId ? (
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
                  <span className="truncate font-mono text-xs">
                    {`${m.tenant_id.slice(0, 8)}…${m.tenant_id.slice(-4)}`}
                  </span>
                  <span className="text-xs text-muted-foreground capitalize">{m.status}</span>
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
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="rounded-full" />}>
          <Avatar className="size-7">
            <AvatarFallback className="bg-primary text-primary-foreground text-xs">
              U
            </AvatarFallback>
          </Avatar>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem render={<Link href="/platform/tenants" />}>
            Platform admin
          </DropdownMenuItem>
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
