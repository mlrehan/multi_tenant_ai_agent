"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { RiskBadge } from "@/components/shared/status-badge";
import { cn } from "@/lib/utils";
import type { PermissionSummary } from "@/lib/types";

/**
 * The permission catalog rendered as a scannable, grouped ledger --
 * monospace codes (this product's actual subject matter), grouped by
 * resource, risk-coded. Two modes from one component: read-only display of
 * an effective-permission set, or a selectable picker for custom-role and
 * override creation. There's no per-permission source breakdown available
 * from the API (the backend only ever returns the final resolved set, not
 * which role/override contributed each entry) -- this shows the resolved
 * set clearly rather than fabricating a breakdown the data can't support.
 */
export function PermissionList({
  permissions,
  selected,
  onToggle,
  highlighted,
  emptyMessage = "No permissions.",
}: {
  permissions: PermissionSummary[];
  selected?: Set<string>;
  onToggle?: (code: string) => void;
  highlighted?: Set<string>;
  emptyMessage?: string;
}) {
  const [search, setSearch] = useState("");
  const selectable = Boolean(onToggle);

  const grouped = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = term
      ? permissions.filter(
          (p) => p.code.toLowerCase().includes(term) || p.description?.toLowerCase().includes(term),
        )
      : permissions;

    const byResource = new Map<string, PermissionSummary[]>();
    for (const permission of filtered) {
      const list = byResource.get(permission.resource) ?? [];
      list.push(permission);
      byResource.set(permission.resource, list);
    }
    return [...byResource.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [permissions, search]);

  if (permissions.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <div className="space-y-4">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter permissions…"
          className="h-8 pl-8 text-sm"
        />
      </div>

      <div className="space-y-5">
        {grouped.map(([resource, items]) => (
          <div key={resource}>
            <h4 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {resource}
            </h4>
            <ul className="space-y-1">
              {items.map((permission) => {
                const isSelected = selected?.has(permission.code) ?? false;
                const isHighlighted = highlighted?.has(permission.code) ?? false;
                return (
                  <li key={permission.code}>
                    <label
                      className={cn(
                        "flex items-start gap-2.5 rounded-md border border-transparent px-2 py-1.5 text-sm",
                        selectable && "cursor-pointer hover:bg-accent/60",
                        isHighlighted && "border-primary/30 bg-accent/40",
                      )}
                    >
                      {selectable && (
                        <Checkbox
                          checked={isSelected}
                          onCheckedChange={() => onToggle?.(permission.code)}
                          className="mt-0.5"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <code className="font-mono text-xs text-foreground">{permission.code}</code>
                          <RiskBadge level={permission.risk_level} />
                        </div>
                        {permission.description && (
                          <p className="mt-0.5 text-xs text-muted-foreground">{permission.description}</p>
                        )}
                      </div>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        {grouped.length === 0 && (
          <p className="text-sm text-muted-foreground">No permissions match &ldquo;{search}&rdquo;.</p>
        )}
      </div>
    </div>
  );
}
