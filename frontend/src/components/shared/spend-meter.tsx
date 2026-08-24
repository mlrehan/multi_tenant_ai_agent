"use client";

/**
 * A used/remaining meter for a token or message allowance.
 *
 * Shared by the platform dashboard and the tenant dashboard so the two cannot
 * disagree about how an allowance is drawn -- the same reason the *threshold*
 * is computed server-side. A tenant owner and the operator looking at the same
 * budget should see the same picture.
 *
 * **`null` renders `?`, never `0`.** An unreadable counter and an unspent one
 * are a whole budget apart, and only one of them means there is room left.
 * This is the same rule the AI Chatbot screen already follows.
 */

import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Progress } from "@/components/ui/progress";

function format(value: number | null): string {
  return value === null ? "?" : value.toLocaleString();
}

export function SpendMeter({
  label,
  used,
  limit,
  remaining,
  runningLow = false,
  unit = "tokens",
  note,
}: {
  label: string;
  used: number | null;
  limit: number | null;
  remaining: number | null;
  runningLow?: boolean;
  unit?: string;
  note?: string;
}) {
  // An uncapped allowance has no meaningful bar: a percentage of infinity is
  // not a number anyone can act on, so the usage is shown as a plain figure.
  const uncapped = limit === null;
  const percent =
    uncapped || used === null || limit === 0
      ? null
      : Math.min(100, Math.round((used / limit) * 100));

  return (
    <div className={cn("rounded-lg border p-4", runningLow && "border-destructive/50 bg-destructive/5")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {format(used)}
            <span className="text-sm font-normal text-muted-foreground">
              {uncapped ? ` ${unit} used` : ` / ${format(limit)}`}
            </span>
          </p>
        </div>
        {runningLow && (
          <span className="flex shrink-0 items-center gap-1 rounded-md bg-destructive/10 px-2 py-1 text-xs font-medium text-destructive">
            <AlertTriangle className="size-3.5" /> Low
          </span>
        )}
      </div>

      {percent !== null && (
        <Progress
          value={percent}
          className={cn("mt-3 h-1.5", runningLow && "[&>*]:bg-destructive")}
        />
      )}

      <p className="mt-2 text-xs text-muted-foreground">
        {uncapped
          ? "No limit set"
          : `${format(remaining)} ${unit} remaining${percent !== null ? ` · ${percent}% used` : ""}`}
        {note ? ` · ${note}` : ""}
      </p>
    </div>
  );
}
