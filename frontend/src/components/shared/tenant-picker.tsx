"use client";

import { useState } from "react";
import { Building2, Check, ChevronsUpDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useTenants } from "@/features/platform/hooks";
import { cn } from "@/lib/utils";

/**
 * Picks a tenant by name or slug instead of by pasted UUID. `useTenants()`
 * has no server-side search or pagination -- unlike the user directory, the
 * full list is already in the query cache, so filtering here is a plain
 * client-side match rather than a debounced request.
 */
export function TenantPicker({
  value,
  onChange,
  id,
}: {
  value: string;
  onChange: (tenantId: string) => void;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const tenants = useTenants();

  const selected = tenants.data?.find((t) => t.id === value);
  const term = search.trim().toLowerCase();
  const filtered = (tenants.data ?? []).filter(
    (t) =>
      !term ||
      t.display_name.toLowerCase().includes(term) ||
      t.slug.toLowerCase().includes(term),
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
          />
        }
      >
        {selected ? (
          <span className="flex min-w-0 items-center gap-2">
            <Building2 className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{selected.display_name}</span>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {selected.slug}
            </span>
          </span>
        ) : value ? (
          <span className="truncate font-mono text-xs">{value}</span>
        ) : (
          <span className="text-muted-foreground">Select a tenant…</span>
        )}
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent className="w-(--anchor-width) min-w-72 gap-0 p-0" align="start">
        <div className="border-b border-border p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or slug…"
              className="h-8 pl-8 text-sm"
            />
          </div>
        </div>
        <div className="max-h-64 overflow-y-auto p-1">
          {tenants.isLoading && (
            <div className="space-y-1 p-1">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          )}
          {filtered.length === 0 && !tenants.isLoading && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              No tenant matches &ldquo;{search}&rdquo;.
            </p>
          )}
          {filtered.map((tenant) => (
            <button
              key={tenant.id}
              type="button"
              onClick={() => {
                onChange(tenant.id);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent",
                tenant.id === value && "bg-accent",
              )}
            >
              <Check
                className={cn(
                  "size-3.5 shrink-0",
                  tenant.id === value ? "opacity-100" : "opacity-0",
                )}
              />
              <span className="min-w-0 flex-1 truncate">{tenant.display_name}</span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {tenant.slug}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
