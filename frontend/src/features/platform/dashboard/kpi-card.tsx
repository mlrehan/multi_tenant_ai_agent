"use client";

import { ArrowDownRight, ArrowRight, ArrowUpRight, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { Trend } from "./format";

/** A tiny line of the daily values, drawn as plain SVG. It shows shape, not
 *  magnitude -- the number beside it carries the magnitude -- so it has no
 *  axis and does not need a chart library's weight on every card. */
function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const step = 100 / (values.length - 1);
  const points = values.map((v, i) => `${(i * step).toFixed(2)},${(28 - (v / max) * 26).toFixed(2)}`);
  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      aria-hidden
      className={cn("h-8 w-24 overflow-visible", className)}
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

const TREND_ICON: Record<Trend, LucideIcon> = {
  up: ArrowUpRight,
  down: ArrowDownRight,
  flat: ArrowRight,
  new: Sparkles,
  none: ArrowRight,
};

function trendClass(trend: Trend, goodWhen: "up" | "down"): string {
  if (trend !== "up" && trend !== "down") return "text-muted-foreground";
  return trend === goodWhen
    ? "text-emerald-700 dark:text-emerald-400"
    : "text-rose-700 dark:text-rose-400";
}

/**
 * One headline number: label, value, how it moved, and its recent shape.
 *
 * `goodWhen` says which direction is good news. More questions is growth;
 * more "not helpful" ratings is not -- colouring every rise green would tell
 * an operator a worsening number was an improvement.
 */
export function KpiCard({
  icon: Icon,
  label,
  value,
  unit,
  trend,
  trendLabel,
  goodWhen = "up",
  detail,
  series,
}: {
  icon: LucideIcon;
  label: string;
  value: string | undefined;
  unit?: string;
  trend?: Trend;
  trendLabel?: string;
  goodWhen?: "up" | "down";
  detail?: string;
  series?: number[];
}) {
  const TrendIcon = trend ? TREND_ICON[trend] : undefined;

  return (
    <div className="flex min-w-0 flex-col justify-between gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="size-4 shrink-0" />
        <span className="truncate text-xs font-medium tracking-wide uppercase">{label}</span>
      </div>
      <div>
        {/* A div, not a p: Skeleton renders a div. The sparkline sits beside
            the value, not the label, so a narrow card wraps neither. */}
        <div className="flex items-end justify-between gap-3">
          <div className="flex items-baseline gap-1.5">
            {value === undefined ? (
              <Skeleton className="h-8 w-16" />
            ) : (
              <span className="text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
            )}
            {unit && value !== undefined && (
              <span className="text-sm text-muted-foreground">{unit}</span>
            )}
          </div>
          {series && <Sparkline values={series} className="mb-1 shrink-0 text-primary/70" />}
        </div>
        {trendLabel && trend && TrendIcon && (
          <p className={cn("mt-1 flex items-center gap-1 text-xs font-medium", trendClass(trend, goodWhen))}>
            <TrendIcon className="size-3.5" />
            {trendLabel}
          </p>
        )}
        {detail && <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>}
      </div>
    </div>
  );
}
