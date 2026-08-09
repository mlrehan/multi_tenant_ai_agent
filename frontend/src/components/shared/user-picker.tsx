"use client";

import { useState } from "react";
import { Check, ChevronsUpDown, Search, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { usePlatformUsers } from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";

/**
 * Picks a platform user by email instead of by pasted UUID.
 *
 * Asking an administrator to type `00000000-0000-0000-0000-000000000000` to
 * name a person is the kind of thing that only looks acceptable to whoever
 * wrote the API — nobody knows a colleague's user id, and a mistyped one either
 * fails opaquely or, worse, silently names the wrong account.
 *
 * Degrades on purpose: the directory needs `platform.users.read`, which a
 * caller holding only `platform.tenants.create` won't have. Rather than showing
 * a broken picker, it falls back to a plain id field and says why.
 */
export function UserPicker({
  value,
  onChange,
  id,
  placeholder = "Search by email…",
}: {
  value: string;
  onChange: (userId: string) => void;
  id?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const users = usePlatformUsers({ search: search || undefined, limit: 8 });

  const directoryUnavailable = isApiError(users.error) && users.error.isForbidden;
  const selected = users.data?.users.find((u) => u.id === value);

  if (directoryUnavailable) {
    return (
      <div className="space-y-1.5">
        <Input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="00000000-0000-0000-0000-000000000000"
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          You don&apos;t have <code className="font-mono">platform.users.read</code>, so the
          directory can&apos;t be searched — paste the user ID instead.
        </p>
      </div>
    );
  }

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
            <UserRound className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{selected.email}</span>
          </span>
        ) : value ? (
          <span className="truncate font-mono text-xs">{value}</span>
        ) : (
          <span className="text-muted-foreground">Select a user…</span>
        )}
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      {/* `--anchor-width` is set by Base UI's Positioner and inherits down to
          the popup, so the list lines up with the trigger. `min-w-72` keeps it
          usable if that var is ever absent. */}
      <PopoverContent className="w-(--anchor-width) min-w-72 gap-0 p-0" align="start">
        <div className="border-b border-border p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={placeholder}
              className="h-8 pl-8 text-sm"
            />
          </div>
        </div>
        <div className="max-h-64 overflow-y-auto p-1">
          {users.isLoading && (
            <div className="space-y-1 p-1">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          )}
          {users.data?.users.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              No user matches &ldquo;{search}&rdquo;.
            </p>
          )}
          {users.data?.users.map((user) => (
            <button
              key={user.id}
              type="button"
              onClick={() => {
                onChange(user.id);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent",
                user.id === value && "bg-accent",
              )}
            >
              <Check
                className={cn(
                  "size-3.5 shrink-0",
                  user.id === value ? "opacity-100" : "opacity-0",
                )}
              />
              <span className="min-w-0 flex-1 truncate">{user.email}</span>
              {user.status !== "active" && (
                <Badge variant="outline" className="shrink-0 text-[10px]">
                  {user.status}
                </Badge>
              )}
            </button>
          ))}
          {users.data && users.data.total > users.data.users.length && (
            <p className="px-2 py-2 text-center text-xs text-muted-foreground">
              {users.data.total - users.data.users.length} more — refine your search.
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
