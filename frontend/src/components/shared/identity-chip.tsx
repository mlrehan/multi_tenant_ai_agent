"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * Renders a UUID/permission-code/other system identifier in monospace with
 * a copy-to-clipboard affordance. This product's actual subject matter is
 * identifiers -- tenant ids, membership ids, permission codes -- so they get
 * first-class, consistent treatment everywhere rather than being formatted
 * as incidental text. Truncates the middle of long values (UUIDs) so the
 * distinguishing start and end stay visible without wrapping tables.
 */
export function IdentityChip({
  value,
  label,
  truncate = true,
  className,
}: {
  value: string;
  label?: string;
  truncate?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const display = truncate && value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;

  async function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            onClick={handleCopy}
            className={cn(
              "group inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-xs text-foreground/90 transition-colors hover:border-primary/40 hover:bg-accent",
              className,
            )}
            aria-label={`Copy ${label ?? "identifier"}: ${value}`}
          />
        }
      >
        <span className="tabular-nums">{display}</span>
        {copied ? (
          <Check className="size-3 text-status-success" />
        ) : (
          <Copy className="size-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        )}
      </TooltipTrigger>
      <TooltipContent className="font-mono text-xs">{value}</TooltipContent>
    </Tooltip>
  );
}
